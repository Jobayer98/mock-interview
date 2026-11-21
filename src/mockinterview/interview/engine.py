"""Step-based interview engine: the seam for web and voice.

The caller owns input and output. `start()` and `step(reply)` yield actions as they become ready, so a fast acknowledgement
reaches the candidate before any slow model call. Everything needed to continue lives in a JSON-serializable SessionState,
so an interview can be stored between HTTP requests or voice turns and resumed by another process.

Pacing and refusals stay in code (I7); the model only words turns and grades. Consume each iterator fully before the next call.
"""
from __future__ import annotations

import copy
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from mockinterview.evaluate.evaluator import EVALUATOR_VERSION, RUBRIC_VERSION, Evaluation, EvaluationError, evaluate_exchange
from mockinterview.llm.base import LLM, MeteredLLM, summarize_calls
from mockinterview.privacy import redact_contact

from . import voice
from .interviewer import INTERVIEWER_VERSION, decide_turn
from .policy import (MAX_DEPTH_FOLLOWUPS, MAX_FOLLOWUPS, MAX_META_REPLIES, MAX_NON_ANSWERS, META_REPLY, classify_reply, followups_allowed,
                     time_is_up)

STATE_SCHEMA_VERSION = 1
MEMORY_ITEMS = 4
RECENT_OPENERS = 3
WARMUP_MEMORY_WORDS = 40
GRADER_THREADS = 2
CONTINUATION_KINDS = {"answer", "filler", "thinking"}  # anything else, such as stop or distress, always gets its own turn
SAY_KINDS = frozenset({"intro", "warmup", "ack", "question", "follow_up", "clarify", "meta_guard", "support", "closing", "outro"})

NON_ANSWER_LINES = {
    "meta": (META_REPLY, "meta_guard"),
    "repeat": (voice.REPEAT, "clarify"),
    "clarify": (voice.CLARIFY, "clarify"),
    "thinking": (voice.TAKE_YOUR_TIME, "clarify"),
    "filler": (voice.STILL_HERE, "clarify"),
    "distress": (voice.SUPPORT, "support"),
    "identity": (voice.IDENTITY, "clarify"),
}


class EngineStateError(RuntimeError):
    """start() or step() was called at the wrong point in the interview."""


@dataclass(frozen=True)
class Say:
    text: str
    kind: str
    question_id: str | None = None


@dataclass(frozen=True)
class Wait:
    """The engine needs the candidate's next reply."""


@dataclass(frozen=True)
class Finished:
    session: dict


Action = Say | Wait | Finished


def evaluation_to_dict(ev: Evaluation) -> dict:
    return {
        "score": ev.score, "model_score": ev.model_score, "confidence": ev.confidence, "injection_attempt": ev.injection_attempt,
        "signals": [{"id": s.id, "text": s.text, "hit": s.hit, "quote": s.quote} for s in ev.signals],
        "red_flags_hit": ev.red_flags_hit, "strengths": ev.strengths, "gaps": ev.gaps,
        "ungrounded_quotes": ev.ungrounded_quotes, "rubric_version": ev.rubric_version,
        "evaluator_version": ev.evaluator_version, "model": ev.llm.model,
        "latency_s": ev.llm.latency_s, "cost_usd": ev.llm.cost_usd,
    }


def _with_integer_rubric_keys(question: dict) -> dict:
    """JSON turns the rubric's integer score levels into strings."""
    return {**question, "rubric": {int(level): text for level, text in question["rubric"].items()}} if "rubric" in question else question


@dataclass
class SessionState:
    """Everything an interview needs to continue. Plain data only, so it survives a JSON round trip."""

    questions: list[dict]
    model: str | None = None
    turn_model: str | None = None
    max_followups: int = MAX_FOLLOWUPS
    warmup: bool = True
    ask_candidate_questions: bool = True
    time_budget_s: float | None = None
    phrasing: voice.Phrasing = field(default_factory=voice.Phrasing)
    phase: str = "new"  # new, warmup, question, closing, finished
    index: int = -1
    previous_type: str = "start"
    active_question: str = ""
    meta_replies: int = 0
    non_answers: int = 0
    records: list[dict] = field(default_factory=list)
    warmup_record: dict | None = None
    memory: list[list[str]] = field(default_factory=list)  # [question id, verified phrase the candidate said]
    recent_openers: list[str] = field(default_factory=list)
    ended_early: bool = False
    ended_reason: str | None = None
    pending_grades: list[int] = field(default_factory=list)  # indexes into records: closed questions still to be graded
    events: list[dict] = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)
    started_at: float = 0.0
    started_clock: float = 0.0
    schema_version: int = STATE_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        if data.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported session state version: {data.get('schema_version')!r}")
        fields = copy.deepcopy(data)
        fields["phrasing"] = voice.Phrasing(**fields["phrasing"])
        fields["questions"] = [_with_integer_rubric_keys(q) for q in fields["questions"]]
        return cls(**fields)


class InterviewEngine:
    """Runs one interview. Holds only what cannot be stored (model clients and running threads); the rest is in `state`."""

    def __init__(self, llm: LLM, state: SessionState, *, turn_llm: LLM | None = None, clock: Callable[[], float] = time.time,
                 grade_in_background: bool = True):
        self.state = state
        metered = MeteredLLM(llm.inner if isinstance(llm, MeteredLLM) else llm, state.llm_calls)
        self._grade_llm = metered.for_purpose("grade")
        self._turn_llm = MeteredLLM(turn_llm, state.llm_calls, "turn") if turn_llm is not None else metered.for_purpose("turn")
        self._clock = clock
        self._grade_in_background = grade_in_background
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[int, Future] = {}

    @classmethod
    def new(cls, llm: LLM, questions: list[dict], *, model: str | None = None, turn_model: str | None = None, max_followups: int = MAX_FOLLOWUPS,
            warmup: bool = True, ask_candidate_questions: bool = True, time_budget_s: float | None = None, phrasing_seed: int | None = None,
            turn_llm: LLM | None = None, clock: Callable[[], float] = time.time, grade_in_background: bool = True) -> "InterviewEngine":
        state = SessionState(questions=questions, model=model, turn_model=turn_model, max_followups=max_followups, warmup=warmup,
                             ask_candidate_questions=ask_candidate_questions, time_budget_s=time_budget_s, phrasing=voice.Phrasing(phrasing_seed),
                             llm_calls=llm.log if isinstance(llm, MeteredLLM) else [], started_at=time.time(), started_clock=clock())
        return cls(llm, state, turn_llm=turn_llm, clock=clock, grade_in_background=grade_in_background)

    @classmethod
    def restore(cls, llm: LLM, data: dict, *, turn_llm: LLM | None = None, clock: Callable[[], float] = time.time,
                grade_in_background: bool = True) -> "InterviewEngine":
        return cls(llm, SessionState.from_dict(data), turn_llm=turn_llm, clock=clock, grade_in_background=grade_in_background)

    def snapshot(self) -> dict:
        """A JSON-serializable copy of the interview. Finished grades are folded in; any still running are redone by whoever resumes."""
        for index, future in list(self._futures.items()):
            if future.done():
                self._store_grade(index, future.result)
        return self.state.to_dict()

    def start(self) -> Iterator[Action]:
        if self.state.phase != "new":
            raise EngineStateError(f"cannot start an interview in phase {self.state.phase!r}")
        return self._start()

    def step(self, reply: str | None) -> Iterator[Action]:
        """Process the candidate's reply. None means they have gone. Contact details are removed here, before anything is stored (I4)."""
        handlers = {"warmup": self._on_warmup, "question": self._on_question, "closing": self._on_closing}
        if self.state.phase not in handlers:
            raise EngineStateError(f"cannot take a reply in phase {self.state.phase!r}")
        return handlers[self.state.phase](None if reply is None else redact_contact(reply)[0])

    def continue_answer(self, reply: str) -> bool:
        """Add words spoken while the interviewer was thinking to the latest answer. Returns False if they need a proper turn."""
        text = redact_contact(reply)[0]
        if classify_reply(text) not in CONTINUATION_KINDS:
            return False
        target = next((record for record in reversed(self.state.records) if record["answers"]), None)
        self._log("candidate", text=text, question_id=target["question_id"] if target else "warmup", reply_kind="continuation")
        if target is not None:
            target["answers"][-1] = f"{target['answers'][-1]} {text}"
        return True

    def settle_grades(self) -> float:
        """Finish every recorded grading job, running any that no thread owns. Returns the seconds spent waiting."""
        started = time.time()
        for index in list(self.state.pending_grades):
            future = self._futures.get(index)
            self._store_grade(index, future.result if future else (lambda job=index: self._grade(job)))
        return round(time.time() - started, 1)

    # --- speaking and logging ---

    def _log(self, type_: str, **data) -> None:
        self.state.events.append({"t": round(time.time(), 3), "type": type_, **data})

    def _say(self, text: str, kind: str, question_id: str | None = None, **extra) -> Say:
        located = {} if question_id is None else {"question_id": question_id}
        self._log("interviewer", text=text, kind=kind, **located, **extra)
        return Say(text, kind, question_id)

    def _acknowledge(self, question_id: str) -> Say:
        """Spoken before any slow work, so the candidate is never left in silence (I17: independent of any score)."""
        return self._say(self.state.phrasing.acknowledgement(), "ack", question_id=question_id)

    def _classify_and_log(self, reply: str, question_id: str) -> str:
        kind = classify_reply(reply)
        self._log("candidate", text="[not stored]" if kind == "crisis" else reply, question_id=question_id, reply_kind=kind)
        return kind

    def _end_for(self, kind: str, question_id: str) -> Say:
        """The candidate asked to stop, or said something a practice interview must not carry on past."""
        self.state.ended_reason = kind
        return self._say(voice.CRISIS if kind == "crisis" else voice.STOPPING, "support" if kind == "crisis" else "outro", question_id=question_id)

    def _elapsed(self) -> float:
        return self._clock() - self.state.started_clock

    # --- phases ---

    def _start(self) -> Iterator[Action]:
        s = self.state
        first = s.questions[0] if s.questions else {}
        yield self._say(voice.opening(len(s.questions), first.get("role_label", voice.DEFAULT_ROLE_LABEL)), "intro")
        if s.warmup:
            s.phase = "warmup"
            yield self._say(voice.WARMUP, "warmup", question_id="warmup")
            yield Wait()
        else:
            yield from self._next_question()

    def _on_warmup(self, reply: str | None) -> Iterator[Action]:
        """Ungraded opener: never sent to the grader, and it costs no LLM call."""
        s = self.state
        if reply is None:
            s.ended_reason, s.ended_early = "disconnect", True
            yield from self._finish()
            return
        kind = self._classify_and_log(reply, "warmup")
        if kind in ("crisis", "stop"):
            s.ended_early = True
            yield self._end_for(kind, "warmup")
            yield from self._finish()
            return
        if kind == "identity":
            yield self._say(voice.IDENTITY.format(question=voice.WARMUP), "clarify", question_id="warmup")
            yield Wait()
            return
        if kind == "answer":
            s.memory.append(["warmup", " ".join(reply.split()[:WARMUP_MEMORY_WORDS])])
            yield self._acknowledge("warmup")
            s.warmup_record = {"question": voice.WARMUP, "answer": reply}
        else:
            distressed = kind == "distress"
            yield self._say(voice.SUPPORT if distressed else s.phrasing.no_problem(), "support" if distressed else "ack", question_id="warmup")
            s.warmup_record = {"question": voice.WARMUP, "answer": None}
        yield from self._next_question()

    def _next_question(self) -> Iterator[Action]:
        s = self.state
        s.index += 1
        if s.index >= len(s.questions):
            yield from self._begin_closing()
            return
        q = s.questions[s.index]
        if time_is_up(self._elapsed(), s.time_budget_s):
            s.ended_reason = "time"
            yield self._say(voice.OUT_OF_TIME, "ack", question_id=q["id"])
            yield from self._begin_closing()
            return
        is_last = len(s.questions) > 1 and s.index == len(s.questions) - 1
        lead_in = s.phrasing.transition(s.previous_type, q["type"], is_last=is_last)
        s.records.append({"question_id": q["id"], "type": q["type"], "topic": q["topic"], "question": q["question"],
                          "answers": [], "followups": [], "fallback_followups": 0, "depth_followups": 0, "turn_errors": 0, "skipped": False,
                          "evaluation": None, "error": None})
        s.phase, s.active_question, s.meta_replies, s.non_answers = "question", q["question"], 0, 0
        yield self._say(f"{lead_in} {q['question']}", "question", question_id=q["id"], transition=lead_in)
        yield Wait()

    def _on_question(self, reply: str | None) -> Iterator[Action]:
        s = self.state
        q, rec = s.questions[s.index], s.records[s.index]
        if reply is None:
            s.ended_reason = "disconnect"
            yield from self._close_question(ended=True)
            return
        kind = self._classify_and_log(reply, q["id"])
        if kind in ("crisis", "stop"):
            yield self._end_for(kind, q["id"])
            yield from self._close_question(ended=True)
            return
        if kind in ("skip", "dont_know"):
            rec["skipped"] = not rec["answers"]  # declining a follow-up does not undo the answer already given
            yield self._say(s.phrasing.no_problem(), "ack", question_id=q["id"])
            yield from self._close_question()
            return
        if kind != "answer":
            yield from self._on_non_answer(kind, q, rec)
            return
        rec["answers"].append(reply)
        yield self._acknowledge(q["id"])
        yield from self._next_turn(q, rec)

    def _on_non_answer(self, kind: str, q: dict, rec: dict) -> Iterator[Action]:
        s = self.state
        s.non_answers += 1
        s.meta_replies += kind == "meta"
        if s.non_answers > MAX_NON_ANSWERS or s.meta_replies > MAX_META_REPLIES:
            rec["skipped"] = not rec["answers"]
            yield self._say(voice.MOVE_ON, "ack", question_id=q["id"])
            yield from self._close_question()
            return
        line, say_kind = NON_ANSWER_LINES[kind]
        yield self._say(line.format(question=s.active_question), say_kind, question_id=q["id"])
        yield Wait()

    def _next_turn(self, q: dict, rec: dict) -> Iterator[Action]:
        """One light-model call decides the next move; code keeps the limits (I7)."""
        s = self.state
        followups_left = s.max_followups - len(rec["followups"])
        if followups_left <= 0 or not followups_allowed(self._elapsed(), s.time_budget_s):
            yield from self._close_question()
            return
        decision = decide_turn(self._turn_llm, q, rec["answers"], rec["followups"], followups_left=followups_left,
                               depth_allowed=rec["depth_followups"] < MAX_DEPTH_FOLLOWUPS, model=s.turn_model,
                               earlier=[quote for question_id, quote in s.memory if question_id != q["id"]],
                               resume_claim=(q.get("personalized_from") or {}).get("quote"), avoid_openers=list(s.recent_openers))
        if decision.remember:
            s.memory = [*s.memory, [q["id"], decision.remember]][-MEMORY_ITEMS:]
        if decision.move == "advance":
            rec["turn_errors"] += int(decision.fallback)
            yield from self._close_question()
            return
        rec["followups"].append(decision.utterance)
        rec["fallback_followups"] += int(decision.fallback)
        rec["depth_followups"] += int(decision.target == "depth")
        s.active_question = decision.utterance
        s.recent_openers = [*s.recent_openers, " ".join(decision.utterance.split()[:2])][-RECENT_OPENERS:]
        yield self._say(decision.utterance, "follow_up", question_id=q["id"], move=decision.move, target=decision.target,
                        fallback=decision.fallback, rejected=decision.rejected)
        yield Wait()

    def _close_question(self, *, ended: bool = False) -> Iterator[Action]:
        s = self.state
        self._schedule_grade(s.index)
        s.previous_type = s.questions[s.index]["type"]
        if ended:
            s.ended_early = True
            yield from self._finish()
        else:
            yield from self._next_question()

    def _begin_closing(self) -> Iterator[Action]:
        s = self.state
        if not s.ask_candidate_questions:
            yield self._say(voice.OUTRO_NO_QUESTIONS, "outro")
            yield from self._finish()
            return
        s.phase = "closing"
        yield self._say(voice.CANDIDATE_QUESTIONS, "closing")
        yield Wait()

    def _on_closing(self, reply: str | None) -> Iterator[Action]:
        kind = None if reply is None else self._classify_and_log(reply, "closing")
        if kind == "crisis":
            yield self._end_for("crisis", "closing")
            yield from self._finish()
            return
        if kind == "identity":
            yield self._say(voice.IDENTITY.format(question=voice.GOODBYE), "outro")
            yield from self._finish()
            return
        asked = reply is not None and not voice.has_no_questions(reply)
        yield self._say(voice.OUTRO_WITH_QUESTION if asked else voice.OUTRO_NO_QUESTIONS, "outro")
        yield from self._finish()

    def _finish(self) -> Iterator[Action]:
        s = self.state
        s.phase = "finished"
        feedback_wait_s = self.settle_grades()
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None
        first = s.questions[0] if s.questions else {}
        yield Finished({
            "versions": {"rubric": first.get("rubric_version", RUBRIC_VERSION), "evaluator": EVALUATOR_VERSION, "interviewer": INTERVIEWER_VERSION,
                         "voice": voice.VOICE_VERSION, "model": s.model, "turn_model": s.turn_model},
            "started_at": s.started_at, "duration_s": round(time.time() - s.started_at, 1), "feedback_wait_s": feedback_wait_s,
            "ended_early": s.ended_early, "ended_reason": s.ended_reason, "warmup": s.warmup_record, "questions": s.records, "events": s.events,
            "llm_calls": s.llm_calls, "cost": summarize_calls(s.llm_calls),
        })

    # --- grading: one job per closed question, recorded in the state ---

    def _grade(self, index: int) -> Evaluation:
        rec = self.state.records[index]
        return evaluate_exchange(self._grade_llm, self.state.questions[index], list(rec["answers"]), list(rec["followups"]), model=self.state.model)

    def _schedule_grade(self, index: int) -> None:
        """Grade once per question, off the conversation path, so the next question is never delayed."""
        if not self.state.records[index]["answers"]:
            return
        self.state.pending_grades.append(index)
        if self._grade_in_background:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=GRADER_THREADS, thread_name_prefix="grader")
            self._futures[index] = self._pool.submit(self._grade, index)
        else:
            self._store_grade(index, lambda: self._grade(index))

    def _store_grade(self, index: int, fetch_evaluation: Callable[[], Evaluation]) -> None:
        rec = self.state.records[index]
        try:
            rec["evaluation"] = evaluation_to_dict(fetch_evaluation())
        except EvaluationError as exc:
            rec["error"] = str(exc)
        self.state.pending_grades.remove(index)
        self._futures.pop(index, None)
